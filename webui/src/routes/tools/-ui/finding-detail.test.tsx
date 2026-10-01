import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RepairFinding } from '../-tools.types';

import { FindingDetail } from './finding-detail';

afterEach(cleanup);

/** A duplicate finding shaped the way the detector writes it. */
const duplicateFinding = (over: Record<string, unknown> = {}): RepairFinding =>
  ({
    id: 1,
    finding_type: 'duplicate_tracks',
    severity: 'info',
    entity_type: 'track',
    entity_id: '1',
    title: 'Duplicate: Creep by Radiohead',
    description: '2 copies found with similar title/artist',
    details: {
      count: 2,
      tracks: [
        {
          id: 1,
          title: 'Creep',
          artist: 'Radiohead',
          album: 'Pablo Honey',
          // MILLISECONDS, as stored in the tracks table
          duration: 238000,
          bitrate: 985,
          file_path: 'Radiohead/Pablo Honey/01-02 - Creep.flac',
        },
        {
          id: 2,
          title: 'Creep',
          artist: 'Radiohead',
          album: 'Pablo Honey',
          duration: 238000,
          bitrate: 900,
          file_path: 'Radiohead/Pablo Honey/01-02 - Creep (1).flac',
        },
      ],
    },
    ...over,
  }) as unknown as RepairFinding;

describe('FindingDetail duplicate rows', () => {
  it('shows the duration as a real time, not raw milliseconds (#1210)', () => {
    const { container } = render(
      <FindingDetail
        finding={duplicateFinding()}
        onKeepDuplicate={vi.fn()}
        onApplyCoverArt={vi.fn()}
      />,
    );
    const text = container.textContent ?? '';
    // 238000ms is 3:58. It read "238000s" in the report screenshot.
    expect(text).toContain('3:58');
    expect(text).not.toContain('238000s');
  });

  it('still shows the bitrate beside it', () => {
    const { container } = render(
      <FindingDetail
        finding={duplicateFinding()}
        onKeepDuplicate={vi.fn()}
        onApplyCoverArt={vi.fn()}
      />,
    );
    expect(container.textContent).toContain('985 kbps');
  });

  it('marks the copy a playlist points at, and keeps it over the better one (jadux)', () => {
    const base = duplicateFinding();
    const tracks = (base.details as { tracks: Record<string, unknown>[] }).tracks;
    const finding = duplicateFinding({
      details: {
        count: 2,
        tracks: [tracks[0], { ...tracks[1], playlists: ['Road Trip', 'Gym'] }],
      },
    });
    const { container } = render(
      <FindingDetail finding={finding} onKeepDuplicate={vi.fn()} onApplyCoverArt={vi.fn()} />,
    );
    const rows = container.querySelectorAll('.repair-detail-subitem');
    expect(rows[0].querySelector('.repair-in-playlist')).toBeNull();
    expect(rows[0].textContent).toContain('REMOVE');
    expect(rows[1].querySelector('.repair-in-playlist')?.textContent).toBe(
      'In playlist: Road Trip, Gym',
    );
    expect(rows[1].textContent).toContain('KEEP');
  });
});
