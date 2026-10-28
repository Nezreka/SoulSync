import { cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewFinding } from '../-review.api';
import type { FindingTypeInfo } from '../../tools/-tools.groups';

import { CompletenessChoice } from './completeness-choice';
import { PlaylistReferences } from './playlist-references';
import { ReconciliationEvidence } from './reconciliation-evidence';
import { SelectedActionDetail } from './selected-action-detail';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows the exact library-only change instead of an empty approval table', () => {
  render(
    <ReconciliationEvidence
      finding={
        {
          id: 1,
          details: { changes: { library_album_title: { old: 'Album (pressing)', new: 'Album' } } },
        } as unknown as ReviewFinding
      }
    />,
  );
  const table = screen.getByRole('table');
  expect(within(table).getByText('Album (pressing)')).toBeVisible();
  expect(within(table).getByText('Album')).toBeVisible();
  expect(within(table).getByText(/Album title.*library/)).toBeVisible();
});

it('confirms a proposed version directly without searching for the same slot again', () => {
  const onAct = vi.fn();
  render(
    <SelectedActionDetail
      finding={
        {
          id: 1,
          finding_type: 'reconciliation_extra',
          title: 'Song (instrumental)',
          file_path: '/music/song.flac',
          details: {
            version_conflict: true,
            replacement_track: {
              source_track_id: 'song',
              name: 'Song',
              disc_number: 2,
              track_number: 11,
            },
            assignment_options: [{ key: 'id:song:2:11', title: 'Song' }],
          },
        } as unknown as ReviewFinding
      }
      itemIndex={0}
      itemCount={1}
      busy={false}
      onAct={onAct}
      onSelect={vi.fn()}
      typeInfo={{ fixable: true } as FindingTypeInfo}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'This is the correct track' }));
  expect(onAct).toHaveBeenCalledWith('fix', 'assign:id:song:2:11');
});

it('collapses large collections while retaining selections during a search', () => {
  const onChoose = vi.fn();
  render(
    <CompletenessChoice
      finding={
        {
          id: 1,
          details: {
            missing_tracks: Array.from({ length: 14 }, (_, index) => ({
              source_track_id: `track-${index}`,
              title: `Song ${index}`,
              track_number: index + 1,
              disc_number: index < 7 ? 1 : 2,
            })),
          },
        } as unknown as ReviewFinding
      }
      busy={false}
      onChoose={onChoose}
    />,
  );
  const summary = screen.getByText('14 missing tracks · 0 selected');
  expect(summary.closest('details')).not.toHaveAttribute('open');
  fireEvent.click(summary);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Song 0', hidden: true }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Find missing tracks' }), {
    target: { value: 'Song 13' },
  });
  expect(screen.queryByText('Song 0')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Keep as partial · 1 wanted' }));
  expect(onChoose).toHaveBeenCalledWith('partial:["id:track-0:1:1"]');
});

it('requires explicit playlist acknowledgement and sends only displayed references', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ playlists: [{ id: 'p', title: 'Favorites' }] }),
    })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true }) });
  vi.stubGlobal('fetch', fetch);
  const refresh = vi.fn();
  render(
    <PlaylistReferences
      caseId="case"
      findingId={5}
      evidenceToken="token"
      path="/music/song.flac"
      removal
      onRecheck={refresh}
    />,
  );
  await screen.findByText('Favorites');
  fireEvent.click(screen.getByRole('button', { name: 'Remove anyway…' }));
  expect(fetch).toHaveBeenCalledTimes(1);
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Favorites')).toBeVisible();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save removal override' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledWith(true));
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    evidence_token: 'token',
    acknowledge_broken_references: true,
    playlist_ids: ['p'],
  });
});
