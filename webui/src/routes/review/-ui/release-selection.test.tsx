import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';

import { ReviewPage } from './review-page';
import { fetchMock, reply, finding, auxiliary } from './support/page';
import { unexpectedRequest } from './support/requests';

it('explains release comparisons without dumping JSON', async () => {
  const ambiguousFinding = {
    ...finding,
    details: {
      ...finding.details,
      expected_title: 'Laid to Rest',
      artist_name: 'Lamb of God',
      acoustid: {
        status: 'ok',
        best_score: 0.98,
        recordings: [
          { title: 'Laid to Rest', artist: 'Lamb of God', score: 0.98 },
          { title: 'Hourglass', artist: 'Lamb of God', score: 0.98 },
        ],
      },
      ambiguity_reasons: [
        'AcoustID returned competing identities not resolved by the other evidence',
      ],
    },
  };
  fetchMock.mockImplementation((url: string) => {
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
              album_thumb_url: '/api/image-cache/local-ashes',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_unresolved'],
              error_count: 0,
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
          album_thumb_url: '/api/image-cache/local-ashes',
          folder: '/music/Lamb of God/Ashes of the Wake',
          count: 1,
          status: 'pending',
          items: [ambiguousFinding],
        }),
      );
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Ashes of the Wake' });
  fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
  await screen.findByRole('heading', { name: 'Compare 2 release candidates' });
  const images = [...document.querySelectorAll('img')];
  expect(images.some((image) => image.src.includes('local-ashes'))).toBe(true);
  expect(images.some((image) => image.src.includes('release-one'))).toBe(true);
  expect(screen.getByText('Assignment settled')).toBeTruthy();
  expect(screen.getByText(/US deluxe edition/)).toBeTruthy();
  expect(screen.queryByText(/"release_id"/)).toBeNull();
});

it('pins the chosen release with its evidence token', async () => {
  let applied = false;
  fetchMock.mockImplementation((url: string, options?: RequestInit) => {
    if (url === '/api/repair/finding-types')
      return Promise.resolve(
        reply({
          types: [
            {
              type: 'reconciliation_release',
              label: 'Release Ambiguity',
              verb: 'Choose Release',
              fixable: true,
              destructive: false,
            },
          ],
        }),
      );
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: applied
            ? []
            : [
                {
                  case_id: 'album:1:ashes',
                  album: 'Ashes of the Wake',
                  artist: 'Lamb of God',
                  pending: 1,
                  priority: 1,
                  finding_types: ['reconciliation_release'],
                  error_count: 0,
                },
              ],
          total: applied ? 0 : 1,
          page: 0,
          limit: 30,
        }),
      );
    if (url.includes('/findings/7/apply')) {
      const body = JSON.parse(options?.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({
        action: 'fix',
        fix_action: '0',
        evidence_token: 'current-evidence',
      });
      applied = true;
      return Promise.resolve(reply({ success: true, message: 'Release pinned' }));
    }
    if (url.startsWith('/api/repair/review-queue/album%3A1%3Aashes?'))
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
              items: [{ ...finding, finding_type: 'reconciliation_release' }],
            }),
      );
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Ashes of the Wake' });
  fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
  const button = await screen.findByRole('button', { name: 'Use this release' });
  fireEvent.click(button);
  await waitFor(() => expect(applied).toBe(true));
});

it('shows edition artwork and colored fit in the album plan', async () => {
  const releaseFinding = {
    ...finding,
    finding_type: 'reconciliation_release',
    details: {
      ...finding.details,
      candidates: [
        {
          source: 'musicbrainz',
          release_id: 'first-release',
          release_title: 'Album',
          image_url: '/api/image-cache/first-release',
          matched: 12,
          total_slots: 12,
          missing: [],
          extra: [],
        },
        {
          source: 'musicbrainz',
          release_id: 'second-release',
          release_title: 'Album (deluxe)',
          matched: 10,
          total_slots: 12,
          missing: ['Bonus 1', 'Bonus 2'],
          extra: ['local.flac'],
        },
      ],
    },
  };
  const decision = {
    id: 'release_conflicts',
    category: 'release_conflicts',
    label: 'Release or edition',
    risk: 'manual',
    summary: 'Multiple releases fit equally well.',
    finding_ids: [7],
    action_count: 1,
  };
  const plan = {
    ready: 0,
    manual: 1,
    destructive: 0,
    blocked: 0,
    total: 1,
    status: 'needs_review',
    blocker_reasons: [],
    can_apply_ready: false,
    can_organize: false,
    decisions: [decision],
  };
  fetchMock.mockImplementation((url: string) => {
    const common = auxiliary(url);
    if (common) return Promise.resolve(common);
    if (url.startsWith('/api/repair/review-queue?'))
      return Promise.resolve(
        reply({
          items: [
            {
              case_id: 'album:1:ashes',
              album: 'Album',
              artist: 'Artist',
              album_thumb_url: '/api/image-cache/local-album',
              pending: 1,
              priority: 1,
              finding_types: ['reconciliation_release'],
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
          album: 'Album',
          artist: 'Artist',
          album_thumb_url: '/api/image-cache/local-album',
          folder: '/music/Album',
          count: 1,
          status: 'pending',
          plan,
          decisions: [decision],
          items: [releaseFinding],
        }),
      );
    return unexpectedRequest(url);
  });
  render(<ReviewPage />);
  await screen.findByRole('heading', { name: 'Album' });
  fireEvent.click(screen.getByRole('tab', { name: 'Release' }));
  expect(
    await screen.findByRole('heading', { name: 'Choose the edition for this album' }),
  ).toBeTruthy();
  const images = [...document.querySelectorAll('img')];
  expect(images.filter((image) => image.src.includes('local-album'))).toHaveLength(2);
  expect(images.some((image) => image.src.includes('first-release'))).toBe(true);
  expect(
    screen.getAllByText('12/12 assigned')[0].closest('[data-tone]')?.getAttribute('data-tone'),
  ).toBe('success');
  expect(screen.getByText('2 missing').closest('[data-tone]')?.getAttribute('data-tone')).toBe(
    'danger',
  );
  expect(screen.getByText('1 extra').closest('[data-tone]')?.getAttribute('data-tone')).toBe(
    'warning',
  );
});
