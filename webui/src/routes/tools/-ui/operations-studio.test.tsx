import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type { RepairJob } from '../-tools.types';

import { Operations } from './operations';
import { OperationsStudio, PLAYBOOK_PRESETS, STRATEGIC_PILLARS } from './operations-studio';

const fetchMock = vi.fn();
const toastSpy = vi.fn();

function routes(map: Record<string, unknown> = {}, fallback: unknown = {}) {
  const merged: Record<string, unknown> = {
    '/api/repair/findings/groups': { groups: mockFindingGroups },
    '/api/repair/finding-types': { types: mockFindingTypes },
    ...map,
  };
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(merged)
      .filter((key) => url.includes(key))
      .sort((a, b) => b.length - a.length)[0];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (hit ? merged[hit] : fallback),
    } as never);
  });
}

const mockJob = (over: Partial<RepairJob> = {}): RepairJob =>
  ({
    job_id: 'audio_corruption_detector',
    display_name: 'Audio Corruption Detector',
    description: 'Finds corrupted FLAC and audio files',
    category: 'Audio quality',
    enabled: true,
    is_running: false,
    interval_hours: 24,
    ...over,
  }) as RepairJob;

const testJobs: RepairJob[] = [
  mockJob({
    job_id: 'audio_corruption_detector',
    display_name: 'Audio Corruption Detector',
    category: 'Audio quality',
    enabled: true,
  }),
  mockJob({
    job_id: 'fake_lossless_detector',
    display_name: 'Fake Lossless Detector',
    category: 'Audio quality',
    enabled: true,
  }),
  mockJob({
    job_id: 'album_tag_consistency',
    display_name: 'Album Tag Consistency',
    category: 'Tags & metadata',
    enabled: true,
  }),
  mockJob({
    job_id: 'missing_lyrics',
    display_name: 'Lyrics Fetcher',
    category: 'Artwork & lyrics',
    enabled: true,
  }),
  mockJob({
    job_id: 'orphan_file_detector',
    display_name: 'Orphan File Detector',
    category: 'Files & storage',
    enabled: true,
  }),
];

const mockFindingGroups: FindingGroup[] = [
  {
    finding_type: 'missing_lyrics',
    pending: 42,
    resolved: 0,
    dismissed: 0,
    total: 42,
    severity_max: 'info',
    job_ids: ['missing_lyrics'],
  },
  {
    finding_type: 'corrupt_audio',
    pending: 3,
    resolved: 0,
    dismissed: 0,
    total: 3,
    severity_max: 'error',
    job_ids: ['audio_corruption_detector'],
  },
  {
    finding_type: 'canonical_version',
    pending: 8,
    resolved: 0,
    dismissed: 0,
    total: 8,
    severity_max: 'info',
    job_ids: ['album_tag_consistency'],
  },
];

const mockFindingTypes: FindingTypeInfo[] = [
  {
    type: 'missing_lyrics',
    label: 'Missing Lyrics',
    verb: 'Apply Lyrics',
    fixable: true,
    destructive: false,
    job_ids: ['missing_lyrics'],
  },
  {
    type: 'corrupt_audio',
    label: 'Corrupt Audio',
    verb: 'Re-download',
    fixable: true,
    destructive: true,
    job_ids: ['audio_corruption_detector'],
  },
  {
    type: 'canonical_version',
    label: 'Canonical Version',
    verb: null,
    fixable: false,
    destructive: false,
    job_ids: ['album_tag_consistency'],
  },
];

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  routes({
    '/api/repair/findings/groups': { groups: mockFindingGroups },
    '/api/repair/finding-types': { types: mockFindingTypes },
  });
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, { showToast: toastSpy });
  try {
    localStorage.clear();
  } catch {}
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('OperationsStudio (Simple Mode)', () => {
  it('renders all 4 Strategic Pillars and their titles', () => {
    const onShowFindings = vi.fn();
    const onSwitchToAdvanced = vi.fn();
    const onChanged = vi.fn();

    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={onChanged}
        onShowFindings={onShowFindings}
        onSwitchToAdvanced={onSwitchToAdvanced}
      />,
    );

    for (const pillar of STRATEGIC_PILLARS) {
      expect(screen.getByText(pillar.title)).not.toBeNull();
    }
  });

  it('renders the Smart Action Triage Center with its 3 authority buckets', async () => {
    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    expect(screen.getByText('Smart Action Triage')).not.toBeNull();
    expect(screen.getByText('Zero-Risk Auto-Fixes')).not.toBeNull();
    expect(screen.getByText('Curator Recommendations')).not.toBeNull();
    expect(screen.getByText('Quarantine & Review')).not.toBeNull();

    // Verify counts populated from mockFindingGroups
    await waitFor(() => {
      expect(screen.getByText('42 Ready')).not.toBeNull();
      expect(screen.getByText('8 Suggestions')).not.toBeNull();
      expect(screen.getByText('⚠️ 3 In Quarantine')).not.toBeNull();
    });
  });

  it('triggers safe bulk fix when "Apply All Safe Fixes" is clicked', async () => {
    routes({
      '/api/repair/findings/groups': { groups: mockFindingGroups },
      '/api/repair/findings/bulk-fix-start': { started: true, total: 42 },
    });

    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getAllByText('⚡ Apply All 42 Safe Fixes').length).toBeGreaterThan(0);
    });

    const safeBtns = screen.getAllByText('⚡ Apply All 42 Safe Fixes');
    fireEvent.click(safeBtns[0]);

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map((c) => String(c[0]));
      expect(calls.some((url) => url.includes('bulk-fix-start'))).toBe(true);
    });
  });

  it('renders Live Mission Control HUD when a job is actively running', async () => {
    const runningJobs = [
      mockJob({
        job_id: 'audio_corruption_detector',
        display_name: 'Audio Corruption Detector',
        is_running: true,
      }),
    ];

    render(
      <OperationsStudio
        jobs={runningJobs}
        progress={{
          audio_corruption_detector: {
            status: 'running',
            progress: 45,
            phase: 'Verifying FLAC frame signatures…',
          },
        }}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    expect(screen.getByText('Active Operation Telemetry')).not.toBeNull();
    expect(screen.getByText('Audio Corruption Detector')).not.toBeNull();
    expect(screen.getByText('Verifying FLAC frame signatures…')).not.toBeNull();
    expect(screen.getByText('⏹ Stop Operation')).not.toBeNull();
  });

  it('opens the biggest type in each bucket when Quarantine or Suggestions is clicked', async () => {
    // the buckets are destructive / fixable, not severity. a severity filter
    // showed something other than what the card counted (orphans are 'info')
    const onShowFindings = vi.fn();
    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={onShowFindings}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Review Suggestions ➔')).not.toBeNull();
    });

    await waitFor(() => {
      expect(screen.getByText('Review Suggestions ➔').closest('button')?.disabled).toBe(false);
    });
    fireEvent.click(screen.getByText('Review Suggestions ➔'));
    expect(onShowFindings).toHaveBeenCalledWith('', { findingType: 'canonical_version' });

    fireEvent.click(screen.getByText('🛡️ Inspect Quarantine ➔'));
    expect(onShowFindings).toHaveBeenCalledWith('', { findingType: 'corrupt_audio' });
  });

  it('renders all 1-Click Playbooks', () => {
    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    for (const playbook of PLAYBOOK_PRESETS) {
      expect(screen.getByText(playbook.title)).not.toBeNull();
    }
  });

  it('launches a playbook when its card is clicked', async () => {
    routes({ '/run': { success: true } });
    const onChanged = vi.fn();

    render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={onChanged}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );

    const audioSweepBtn = screen.getByText('Audio Fidelity Sweep').closest('button');
    expect(audioSweepBtn).not.toBeNull();

    fireEvent.click(audioSweepBtn!);

    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.stringContaining('Audio Fidelity Sweep'),
        'info',
      );
    });
  });

  it.each([
    [
      'Full Library Tune-Up',
      [
        'audio_corruption_detector',
        'album_tag_consistency',
        'missing_lyrics',
        'missing_cover_art',
        'empty_folder_cleaner',
      ],
    ],
    [
      'Audio Fidelity Sweep',
      ['audio_corruption_detector', 'fake_lossless_detector', 'short_preview_track'],
    ],
    [
      'Metadata Polish',
      ['album_tag_consistency', 'comma_artist_splitter', 'genre_cleanup', 'track_number_repair'],
    ],
    ['Media Enrichment', ['missing_lyrics', 'missing_cover_art', 'replaygain_filler']],
  ] as const)('queues every supported job in %s', async (title, expectedIds) => {
    // These are the music repair registry's actual IDs, rather than the old
    // UI's aliases. A missing alias silently dropped a playbook's work.
    const catalogueIds = [
      'audio_corruption_detector',
      'fake_lossless_detector',
      'short_preview_track',
      'album_tag_consistency',
      'comma_artist_splitter',
      'genre_cleanup',
      'suspect_album_tag_detector',
      'track_number_repair',
      'mbid_mismatch_detector',
      'missing_lyrics',
      'missing_cover_art',
      'replaygain_filler',
      'orphan_file_detector',
      'dead_file_cleaner',
      'empty_folder_cleaner',
    ];
    routes({ '/run': { success: true } });
    render(
      <OperationsStudio
        jobs={catalogueIds.map((job_id) => mockJob({ job_id }))}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(title).closest('button')!);
    await waitFor(() => {
      const runUrls = fetchMock.mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.endsWith('/run'));
      expect(runUrls).toEqual(expectedIds.map((id) => `/api/repair/jobs/${id}/run`));
    });
  });

  it('navigates to advanced mode when "Inspect in Advanced" is clicked', () => {
    const onSwitchToAdvanced = vi.fn();

    const { container } = render(
      <OperationsStudio
        jobs={testJobs}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={onSwitchToAdvanced}
      />,
    );

    const inspectLinks = container.querySelectorAll('.operations-pillar-inspect-link');
    expect(inspectLinks.length).toBeGreaterThan(0);

    fireEvent.click(inspectLinks[0]);
    expect(onSwitchToAdvanced).toHaveBeenCalledWith('Audio quality');
  });
});

describe('Operations component mode switcher', () => {
  it('defaults to Simple Mode and renders the mode toggle bar', () => {
    const { container } = render(
      <Operations
        jobs={testJobs}
        error={false}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onHelp={vi.fn()}
        onShowFindings={vi.fn()}
      />,
    );

    expect(container.querySelector('.operations-mode-bar')).not.toBeNull();
    expect(container.querySelector('.operations-studio')).not.toBeNull();

    const simpleBtn = container.querySelector('.operations-mode-toggle-btn[aria-selected="true"]');
    expect(simpleBtn?.textContent).toContain('Simple Mode');
  });

  it('switches between Simple and Advanced modes when toggle buttons are clicked', () => {
    const onModeChange = vi.fn();

    const { container } = render(
      <Operations
        jobs={testJobs}
        error={false}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onHelp={vi.fn()}
        onShowFindings={vi.fn()}
        onModeChange={onModeChange}
      />,
    );

    // Click Advanced Mode button
    const advancedBtn = [...container.querySelectorAll('.operations-mode-toggle-btn')].find((btn) =>
      btn.textContent?.includes('Advanced Mode'),
    );
    expect(advancedBtn).not.toBeUndefined();

    fireEvent.click(advancedBtn!);

    expect(onModeChange).toHaveBeenCalledWith('advanced');
    expect(localStorage.getItem('soulsync_operations_mode')).toBe('advanced');

    // Advanced mode reveals the repair families container
    const families = container.querySelector('.repair-families') as HTMLElement;
    expect(families.hidden).toBe(false);
  });
});
