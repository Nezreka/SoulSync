import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FindingGroup, RepairJob } from '../-tools.types';

import { Operations } from './operations';
import {
  OperationsStudio,
  PLAYBOOK_PRESETS,
  STRATEGIC_PILLARS,
} from './operations-studio';

const fetchMock = vi.fn();
const toastSpy = vi.fn();

function routes(map: Record<string, unknown>, fallback: unknown = {}) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map)
      .filter((key) => url.includes(key))
      .sort((a, b) => b.length - a.length)[0];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (hit ? map[hit] : fallback),
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
    job_id: 'lyrics_fetcher',
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
    label: 'Missing Lyrics',
    verb: 'Apply Lyrics',
    count: 42,
    fixable: true,
    destructive: false,
    job_ids: ['lyrics_fetcher'],
  },
  {
    finding_type: 'corrupt_audio',
    label: 'Corrupt Audio',
    verb: 'Re-download',
    count: 3,
    fixable: true,
    destructive: true,
    job_ids: ['audio_corruption_detector'],
  },
  {
    finding_type: 'canonical_version',
    label: 'Canonical Version',
    verb: null,
    count: 8,
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
      expect(screen.getByText('⚡ Apply All 42 Safe Fixes')).not.toBeNull();
    });

    const safeBtn = screen.getByText('⚡ Apply All 42 Safe Fixes');
    fireEvent.click(safeBtn);

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
    const advancedBtn = [...container.querySelectorAll('.operations-mode-toggle-btn')].find(
      (btn) => btn.textContent?.includes('Advanced Mode'),
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
