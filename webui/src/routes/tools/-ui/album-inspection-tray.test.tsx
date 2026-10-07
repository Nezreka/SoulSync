import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RepairFinding } from '../-tools.types';

import { fetchRepairFindings, fixFinding } from '../-tools.api';
import { AlbumInspectionTray } from './album-inspection-tray';

vi.mock('../-tools.api', () => ({
  fetchRepairFindings: vi.fn(),
  fixFinding: vi.fn(async () => ({ success: true })),
  dismissFinding: vi.fn(),
  reopenFinding: vi.fn(),
}));

const GROUP = {
  group_by: 'album' as const,
  key: 'Aphex Twin SAW',
  artist: 'Aphex Twin',
  album: 'SAW',
  count: 2,
  worst_score: null,
  best_score: null,
  worst_quality: '',
  best_quality: '',
  album_thumb_url: null,
  artist_thumb_url: null,
  artist_id: null,
  first_seen: null,
  last_seen: null,
};

function finding(patch: Partial<RepairFinding> & { id: number; finding_type: string }) {
  return {
    job_id: 'job',
    severity: 'warning',
    status: 'pending',
    title: 'Xtal',
    entity_type: 'track',
    entity_id: null,
    details: { album_title: 'SAW', artist_name: 'Aphex Twin', track_title: 'Xtal' },
    ...patch,
  } as RepairFinding;
}

function renderTray(findings: RepairFinding[]) {
  vi.mocked(fetchRepairFindings).mockResolvedValue({
    items: findings,
    total: findings.length,
    page: 0,
  });
  const onInspectRedownload = vi.fn();
  render(
    <AlbumInspectionTray
      group={GROUP}
      status="pending"
      onClose={() => {}}
      onFixFinding={async () => {}}
      onDismissFinding={async () => {}}
      onInspectRedownload={onInspectRedownload}
      onRefresh={() => {}}
    />,
  );
  return onInspectRedownload;
}

afterEach(() => vi.clearAllMocks());

describe('the album inspection tray', () => {
  it('offers a re-download only for a finding with a track behind it', async () => {
    // A fake-lossless finding reports a FILE (entity_id null). Its finding id is
    // not a track id: opening the re-download dialog with it searched for, and
    // replaced, the file of whichever track happened to have that id.
    const onInspectRedownload = renderTray([
      finding({ id: 7, finding_type: 'fake_lossless', entity_type: 'file' }),
      finding({ id: 8, finding_type: 'corrupt_audio', entity_id: '42', title: 'Ptolemy' }),
    ]);

    const buttons = await screen.findAllByRole('button', { name: /re-download/i });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    expect(onInspectRedownload).toHaveBeenCalledWith(expect.objectContaining({ id: 8 }));
  });

  it('resolves only the findings that have a fix', async () => {
    window.showConfirmDialog = vi.fn(async () => true);
    renderTray([
      finding({ id: 7, finding_type: 'fake_lossless', entity_type: 'file' }),
      finding({ id: 9, finding_type: 'missing_lyrics', entity_id: '43' }),
    ]);

    fireEvent.click(await screen.findByRole('button', { name: /resolve album/i }));

    await waitFor(() => expect(fixFinding).toHaveBeenCalledTimes(1));
    expect(fixFinding).toHaveBeenCalledWith(9);
  });
});
