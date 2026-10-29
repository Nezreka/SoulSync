import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { ApplyPlanPreview } from './apply-plan-preview';

afterEach(cleanup);

it('groups overlapping changes while retaining one action total and exact values', () => {
  render(
    <ApplyPlanPreview
      expanded
      destinations={['Artist/Album/Disc 1', 'Artist/Album/Disc 2']}
      preview={{
        signature: 'snapshot',
        action_count: 1,
        file_actions: 0,
        albums: [
          {
            album: 'Album',
            case_id: 'case',
            actions: [
              {
                id: 1,
                title: 'Song',
                file_path: '/music/song.flac',
                description: 'Update Song',
                changes: [
                  { field: 'track_number', old: 12, new: 1, kind: 'numbering' },
                  { field: 'title', old: 'Old title', new: 'Song', kind: 'corrections' },
                ],
              },
            ],
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('1 selected action')).toBeVisible();
  expect(screen.getByText('Disc and track numbering')).toBeVisible();
  fireEvent.click(screen.getByText('Metadata corrections'));
  expect(screen.getByText('Old title')).toBeVisible();
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Artist/Album');
});

it('separates exact track requests from quarantine and labels both ends of a folder move', () => {
  render(
    <ApplyPlanPreview
      expanded
      destinations={[{ current: 'Artist/Old pressing', planned: 'Artist/Approved album' }]}
      preview={{
        signature: 'preview',
        file_actions: 1,
        action_count: 1,
        albums: [
          {
            album: 'Approved album',
            case_id: 'a',
            actions: [
              {
                id: 1,
                description: 'Replace Song',
                title: 'Song',
                requests: [{ title: 'Song (live)', release: 'Approved edition', state: 'planned' }],
                file_decision: {
                  title: 'Song',
                  keep: [],
                  quarantine: ['/library/song.mp3'],
                  request: 'Song (live)',
                },
              },
            ],
          },
        ],
      }}
    />,
  );
  const requests = screen.getByLabelText('Request tracks');
  expect(requests).toHaveTextContent('Song (live)');
  expect(requests).toHaveTextContent('Approved edition');
  expect(requests).toHaveTextContent('Add to wishlist');
  expect(screen.getByLabelText('File decisions included')).not.toHaveTextContent('Song (live)');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Current folder');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Planned folder');
  expect(screen.getByLabelText('Destination folders')).toHaveTextContent('Artist/Approved album');
});

it('searches albums and artists without silently changing the batch selection', () => {
  const onSelectionChange = vi.fn();
  render(
    <ApplyPlanPreview
      expanded={false}
      destinations={[]}
      onSelectionChange={onSelectionChange}
      preview={{
        signature: 'batch',
        action_count: 2,
        file_actions: 0,
        albums: [
          {
            case_id: 'one',
            album: 'Northbound',
            artist: 'First artist',
            actions: [{ id: 1, description: 'Update title' }],
          },
          {
            case_id: 'two',
            album: 'Southbound',
            artist: 'Second artist',
            actions: [{ id: 2, description: 'Update title' }],
          },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Search ready albums' }), {
    target: { value: 'Second artist' },
  });
  expect(screen.getByText('2 of 2 albums selected')).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: /Northbound/ })).toBeNull();
  expect(onSelectionChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Select only shown albums' }));
  expect(onSelectionChange).toHaveBeenLastCalledWith({ case_ids: ['two'], organize_case_ids: [] });
  expect(screen.getByText('1 selected action')).toBeVisible();
});
