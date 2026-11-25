import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { KeepBestCopy } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { copyPlaylistNotes, keepBestSummary } from '../-tools.duplicate-playlists';
import { FindingDetail } from './finding-detail';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const copyRow = (over: Partial<KeepBestCopy> = {}): KeepBestCopy => ({
  file_id: 2,
  action: 'remove',
  path: '/music/A/song.mp3',
  playlists: [],
  ignored_playlists: [],
  playlists_covered: [],
  playlist_moves: [],
  playlists_blocked: '',
  protected_reasons: [],
  ...over,
});

describe('a duplicate copy says what happens to its playlists (#2172)', () => {
  it('in the same playlist as the kept copy, nothing is lost', () => {
    expect(
      copyPlaylistNotes(copyRow({ playlists: ['Road Trip'], playlists_covered: ['Road Trip'] })),
    ).toEqual(['In "Road Trip" with the kept copy, so no playlist loses the song.']);
  });

  it('names a playlist set to never protect, and an entry that moves', () => {
    expect(
      copyPlaylistNotes(
        copyRow({
          playlists: ['Unsorted', 'Chill'],
          ignored_playlists: ['Unsorted'],
          playlist_moves: ['Chill'],
        }),
      ),
    ).toEqual([
      'Its entry in "Chill" moves to the kept copy, same place.',
      'In "Unsorted", which you set to never protect a copy.',
    ]);
  });

  it('says why a copy stays and how to change it', () => {
    const [note] = copyPlaylistNotes(
      copyRow({ action: 'protected', playlists: ['Gym'], playlists_blocked: 'not_in_kept' }),
    );
    expect(note).toBe(
      'Stays: it is in "Gym", which the kept copy is not in. Turn on moving playlist entries to keep that playlist whole and remove it.',
    );
    expect(
      copyPlaylistNotes(
        copyRow({ action: 'protected', playlists: ['Gym'], playlists_blocked: 'same_server_item' }),
      )[0],
    ).toContain("can't tell which file it plays");
  });

  it('sums the plan up for the confirm dialog', () => {
    expect(
      keepBestSummary([
        copyRow({ file_id: 1, action: 'keep', path: '/music/B/song.flac' }),
        copyRow({ playlists: ['Chill'], playlist_moves: ['Chill'] }),
        copyRow({
          file_id: 3,
          action: 'protected',
          playlists: ['Gym'],
          playlists_blocked: 'not_in_kept',
        }),
      ]),
    ).toBe(
      'Keeps song.flac. Moves 1 copy to recoverable quarantine. Points its entry in "Chill" at the kept copy first, in the same place. 1 copy stays, as listed.',
    );
  });
});

const finding = (): RepairFinding =>
  ({
    id: 4,
    finding_type: 'native_duplicate_tracks',
    severity: 'info',
    status: 'pending',
    entity_type: 'track',
    entity_id: 'lib2:10',
    title: 'Duplicate: Song by Artist',
    description: '',
    details: {
      schema: 'native_duplicate_review/v1',
      recommended_file_id: 1,
      tracks: [
        { file_id: 1, track_id: 10, title: 'Song', artist: 'Artist', playlists: ['Road Trip'] },
        {
          file_id: 2,
          track_id: 11,
          title: 'Song',
          artist: 'Artist',
          playlists: ['Road Trip', 'Chill'],
          protected_reasons: ['playlist_reference'],
        },
      ],
    },
  }) as unknown as RepairFinding;

describe('the duplicate finding uses the live plan (#2172)', () => {
  it('shows the plan, refetches it when moving is turned on, and confirms with it', async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const action = decodeURIComponent(String(url).split('action=')[1] || '');
        asked.push(action);
        const move = action.includes(':move');
        return new Response(
          JSON.stringify({
            success: true,
            move_playlist_entries: move,
            copies: [
              copyRow({
                file_id: 1,
                action: 'keep',
                path: '/m/B/song.flac',
                playlists: ['Road Trip'],
              }),
              move
                ? copyRow({
                    playlists: ['Road Trip', 'Chill'],
                    playlists_covered: ['Road Trip'],
                    playlist_moves: ['Chill'],
                  })
                : copyRow({
                    action: 'protected',
                    playlists: ['Road Trip', 'Chill'],
                    playlists_covered: ['Road Trip'],
                    playlists_blocked: 'not_in_kept',
                    protected_reasons: ['playlist_reference'],
                  }),
            ],
          }),
          { status: 200 },
        );
      }),
    );
    const keep = vi.fn();
    render(<FindingDetail finding={finding()} onKeepDuplicate={keep} onApplyCoverArt={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/which the kept copy is not in/)).toBeTruthy());
    expect(asked[0]).toBe('keep_best');
    expect(screen.getByText('PROTECTED')).toBeTruthy();

    fireEvent.click(screen.getByLabelText(/Move playlist entries to the kept copy/));
    await waitFor(() =>
      expect(
        screen.getByText('Its entry in "Chill" moves to the kept copy, same place.'),
      ).toBeTruthy(),
    );
    expect(asked).toContain('keep_best:move');
    expect(screen.getByText('QUARANTINE')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Keep Best' }));
    await waitFor(() => expect(keep).toHaveBeenCalled());
    const [id, action, message] = keep.mock.calls[0];
    expect([id, action]).toEqual([4, 'keep_best:move']);
    expect(message).toContain('Points its entry in "Chill" at the kept copy first');
  });
});
