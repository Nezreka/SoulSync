import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AdlDeletedEntry, AdlDownload, AdlQuarantineEntry } from '../-adl.types';
import type { ReviewActionHandlers } from './adl-review';

import {
  AdlDeletedRow,
  AdlQuarantineList,
  AdlQuarantineRow,
  AdlReviewBanner,
  AdlUnverifiedRow,
} from './adl-review';

afterEach(() => {
  cleanup();
  delete window.formatHistoryTime;
});

beforeEach(() => {
  window.formatHistoryTime = () => '5h ago';
});

const handlers = (): ReviewActionHandlers => ({
  onPlay: vi.fn(),
  onCompare: vi.fn(),
  onAudit: vi.fn(),
  onApprove: vi.fn(),
  onDelete: vi.fn(),
});

const dl = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 'history-42',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'completed',
    progress: 100,
    error: null,
    verification_status: 'unverified',
    batch_id: '',
    batch_name: '',
    batch_source: 'soulseek',
    playlist_id: '',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: 'FLAC',
    is_persistent_history: true,
    created_at: '2026-07-30T07:00:00Z',
    ...over,
  }) as AdlDownload;

const entry = (over: Partial<AdlQuarantineEntry> = {}): AdlQuarantineEntry =>
  ({
    id: 'q1',
    filename: 'bad.quarantined',
    original_filename: 'bad.flac',
    reason: 'duration mismatch',
    expected_track: 'Xtal',
    expected_artist: 'Aphex Twin',
    group_key: 'g1',
    timestamp: '2026-07-30T10:00:00Z',
    size_bytes: 1000,
    has_full_context: true,
    trigger: 'integrity',
    source_username: 'somepeer',
    source_filename: 'peer/bad.flac',
    thumb_url: '',
    quality: 'FLAC',
    ...over,
  }) as AdlQuarantineEntry;

describe('AdlUnverifiedRow', () => {
  it('renders the reason badge, quality chip and time', () => {
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(container.querySelector('.verif-rb-unv')?.textContent).toBe('ACOUSTID UNCONFIRMED');
    expect(container.querySelector('.adl-quality-chip')?.textContent).toBe('FLAC');
    expect(container.querySelector('.verif-time')?.textContent).toBe('5h ago');
  });

  it('omits the time when the row has no timestamp', () => {
    // Live task rows carry no created_at; an empty span would be noise.
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl({ created_at: undefined })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.verif-time')).toBeNull();
  });

  it('shows FORCE-IMPORTED for a force-imported row', () => {
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl({ verification_status: 'force_imported' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.verif-rb-force')?.textContent).toBe('FORCE-IMPORTED');
  });

  it('hides the detail panel until opened', () => {
    const closed = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(
      (closed.container.querySelector('.verif-quar-details') as HTMLElement).style.display,
    ).toBe('none');
    cleanup();
    const open = render(
      <AdlUnverifiedRow dl={dl()} open onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect((open.container.querySelector('.verif-quar-details') as HTMLElement).style.display).toBe(
      '',
    );
  });

  it('shows File and Downloaded only for a persistent-history row', () => {
    // Live task rows carry neither field — the two server builders differ.
    const withFile = render(
      <AdlUnverifiedRow
        dl={dl({ file_path: '/music/x.flac', created_at: '2026-07-30' })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(withFile.container.textContent).toContain('/music/x.flac');
    expect(withFile.container.textContent).toContain('Downloaded:');

    cleanup();
    const live = render(
      <AdlUnverifiedRow
        dl={dl({ file_path: undefined, created_at: undefined })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(live.container.textContent).not.toContain('File:');
    expect(live.container.textContent).not.toContain('Downloaded:');
  });

  it('explains force-imported and unverified differently', () => {
    const forced = render(
      <AdlUnverifiedRow
        dl={dl({ verification_status: 'force_imported' })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(forced.container.textContent).toContain('retry budget was exhausted');
    cleanup();
    const unv = render(
      <AdlUnverifiedRow dl={dl()} open onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(unv.container.textContent).toContain('could not hard-confirm');
  });

  it('toggles on a row click but not on an action click', () => {
    const onToggle = vi.fn();
    const acts = handlers();
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={onToggle} handlers={acts} />,
    );
    fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    expect(onToggle).toHaveBeenCalledTimes(1);

    fireEvent.click(container.querySelector('.verif-act-play') as HTMLElement);
    expect(acts.onPlay).toHaveBeenCalled();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('renders no action buttons without a history id to act on', () => {
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={null} />,
    );
    expect(container.querySelectorAll('.verif-act')).toHaveLength(0);
    // The row still renders — it is informational, not missing.
    expect(container.querySelector('.adl-row-title')?.textContent).toBe('Xtal');
  });

  it('shows a busy glyph while a comparison runs', () => {
    const acts = handlers();
    acts.onCompare = (setBusy) => setBusy(true);
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={acts} />,
    );
    const compare = container.querySelectorAll('.verif-act')[1] as HTMLButtonElement;
    expect(compare.textContent).toBe('⇆');
    fireEvent.click(compare);
    expect(compare.textContent).toBe('…');
    expect(compare.disabled).toBe(true);
  });
});

describe('AdlQuarantineRow', () => {
  it('labels the trigger, falling back to QUARANTINED', () => {
    const { container } = render(
      <AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(container.querySelector('.verif-rb-int')?.textContent).toBe('DURATION / INTEGRITY');

    cleanup();
    const unknown = render(
      <AdlQuarantineRow
        entry={entry({ trigger: 'brand_new' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(unknown.container.querySelector('.verif-reason-badge')?.textContent).toBe('QUARANTINED');
  });

  it('collapses a peer name to Soulseek but keeps a service name', () => {
    const peer = render(
      <AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(peer.container.querySelector('.adl-row-batch')?.textContent).toBe('Soulseek');
    cleanup();
    const service = render(
      <AdlQuarantineRow
        entry={entry({ source_username: 'tidal' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(service.container.querySelector('.adl-row-batch')?.textContent).toBe('Tidal');
  });

  it('offers Approve with full context and Recover without it', () => {
    const full = render(
      <AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    const approve = full.container.querySelector('.verif-act-ok') as HTMLElement;
    // labeled since the redesign: glyph + the word
    expect(approve.textContent).toBe('✔Approve');
    expect(approve.getAttribute('title')).toContain('re-import this exact file');

    cleanup();
    const legacy = render(
      <AdlQuarantineRow
        entry={entry({ has_full_context: false })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    const recover = legacy.container.querySelector('.verif-act-ok') as HTMLElement;
    expect(recover.textContent).toBe('⤴Recover');
    expect(recover.getAttribute('title')).toContain('Recover to Staging');
  });

  it('says so when the sidecar has no details', () => {
    const { container } = render(
      <AdlQuarantineRow
        entry={entry({ reason: '', source_username: '', source_filename: '', timestamp: '' })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.verif-quar-details')?.textContent).toBe(
      'No further details in the sidecar.',
    );
  });
});

describe('AdlQuarantineList grouping', () => {
  const listProps = {
    openDetails: new Set<string>(),
    openGroups: new Set<string>(),
    onToggleDetails: vi.fn(),
    onToggleGroup: vi.fn(),
    handlersFor: () => handlers(),
  };

  it('renders a lone candidate as a plain row', () => {
    const { container } = render(
      <AdlQuarantineList {...listProps} groups={[{ key: 'g1', members: [entry()] }]} />,
    );
    expect(container.querySelectorAll('.verif-quar-row')).toHaveLength(1);
    expect(container.querySelector('.verif-quar-alt-btn')).toBeNull();
  });

  it('folds alternatives behind a toggle', () => {
    // Six rejected candidates for one track should cost one row, not six.
    const members = [entry(), entry({ id: 'q2' }), entry({ id: 'q3' })];
    const { container } = render(
      <AdlQuarantineList {...listProps} groups={[{ key: 'g1', members }]} />,
    );
    const button = container.querySelector('.verif-quar-alt-btn') as HTMLElement;
    expect(button.textContent).toBe('▾ 2 more');
    expect(container.querySelector('.verif-quar-alt-members')?.className).not.toContain('vqg-open');
  });

  it('opens the alternatives when the group is expanded', () => {
    const members = [entry(), entry({ id: 'q2' })];
    const { container } = render(
      <AdlQuarantineList
        {...listProps}
        openGroups={new Set(['g1'])}
        groups={[{ key: 'g1', members }]}
      />,
    );
    expect(container.querySelector('.verif-quar-alt-btn')?.textContent).toBe('▴ 1 more');
    expect(container.querySelector('.verif-quar-alt-members')?.className).toContain('vqg-open');
  });

  it('singularises the alternatives tooltip', () => {
    const { container } = render(
      <AdlQuarantineList
        {...listProps}
        groups={[{ key: 'g1', members: [entry(), entry({ id: 'q2' })] }]}
      />,
    );
    expect(container.querySelector('.verif-quar-alt-btn')?.getAttribute('title')).toBe(
      'Show 1 more alternative candidate for this track',
    );
  });
});

describe('AdlReviewBanner', () => {
  const props = {
    subView: 'unverified' as const,
    acoustidEnabled: true,
    unverifiedCount: 3,
    quarantineCount: 2,
    quarantineLoaded: true,
    onSubView: vi.fn(),
    onApproveAll: vi.fn(),
    onCleanOrphans: vi.fn(),
    onDeleteAll: vi.fn(),
    onQuarantineApproveAll: vi.fn(),
    onQuarantineClearAll: vi.fn(),
    deletedCount: 4,
    selectedCount: 0,
    onApproveSelected: vi.fn(),
    onDeleteSelected: vi.fn(),
    onRestoreAllDeleted: vi.fn(),
    onEmptyDeleted: vi.fn(),
  };

  it('shows all three pills with counts', () => {
    const { container } = render(<AdlReviewBanner {...props} />);
    const pills = [...container.querySelectorAll('.adl-pill')].map((p) => p.textContent);
    expect(pills).toEqual(['⚠ Unverified (3)', '🛡 Quarantine (2)', '🗑 Deleted (4)']);
  });

  it('hides the unverified pill when no such queue can exist', () => {
    const { container } = render(<AdlReviewBanner {...props} acoustidEnabled={false} />);
    const pills = [...container.querySelectorAll('.adl-pill')].map((p) => p.textContent);
    // the deleted bin exists regardless of acoustid - only unverified hides
    expect(pills).toEqual(['🛡 Quarantine (2)', '🗑 Deleted (4)']);
  });

  it('shows the deleted pill with no count until the bin has been loaded', () => {
    const { container } = render(<AdlReviewBanner {...props} deletedCount={null} />);
    const pills = [...container.querySelectorAll('.adl-pill')].map((p) => p.textContent);
    expect(pills[2]).toBe('🗑 Deleted');
  });

  it('omits the quarantine count until it is actually known', () => {
    // Showing (0) before the fetch lands would read as "none quarantined".
    const { container } = render(<AdlReviewBanner {...props} quarantineLoaded={false} />);
    expect(container.querySelectorAll('.adl-pill')[1].textContent).toBe('🛡 Quarantine');
  });

  it('offers Clean orphaned only in the unverified view', () => {
    const unv = render(<AdlReviewBanner {...props} />);
    expect(unv.container.textContent).toContain('🧹 Clean orphaned');
    expect(unv.container.textContent).toContain('🗑 Delete all');

    cleanup();
    // A quarantined file IS the file — it cannot be orphaned.
    const quar = render(<AdlReviewBanner {...props} subView="quarantine" />);
    expect(quar.container.textContent).not.toContain('Clean orphaned');
    expect(quar.container.textContent).toContain('🗑 Clear all');
  });

  it('narrows the bulk buttons to the selection when rows are checked', () => {
    const onApproveSelected = vi.fn();
    const { container } = render(
      <AdlReviewBanner {...props} selectedCount={2} onApproveSelected={onApproveSelected} />,
    );
    expect(container.textContent).toContain('✔ Approve selected (2)');
    expect(container.textContent).toContain('🗑 Delete selected (2)');
    expect(container.textContent).not.toContain('Approve all');
    fireEvent.click(
      [...container.querySelectorAll('.adl-filter-banner-clear')].find((b) =>
        b.textContent?.includes('Approve selected'),
      ) as HTMLElement,
    );
    expect(onApproveSelected).toHaveBeenCalled();
  });

  it('hides bulk buttons over an empty view — a promise the click cannot keep', () => {
    const unv = render(<AdlReviewBanner {...props} unverifiedCount={0} />);
    expect(unv.container.textContent).not.toContain('Approve all');
    cleanup();
    const quar = render(<AdlReviewBanner {...props} subView="quarantine" quarantineCount={0} />);
    expect(quar.container.textContent).not.toContain('Approve all');
    cleanup();
    const del = render(<AdlReviewBanner {...props} subView="deleted" deletedCount={0} />);
    expect(del.container.textContent).not.toContain('Empty bin');
  });

  it('routes each bulk button to its own handler', () => {
    const onApproveAll = vi.fn();
    const onDeleteAll = vi.fn();
    const { container } = render(
      <AdlReviewBanner {...props} onApproveAll={onApproveAll} onDeleteAll={onDeleteAll} />,
    );
    const buttons = [...container.querySelectorAll('.adl-filter-banner-clear')];
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[2]);
    expect(onApproveAll).toHaveBeenCalled();
    expect(onDeleteAll).toHaveBeenCalled();
  });
});

describe('AdlDeletedRow source labels', () => {
  const entry = (source: string | null): AdlDeletedEntry => ({
    id: 'd1',
    name: '01-02 New Pammy.flac',
    rel: 'album_bundle_orphans/b_stalled/01-02 New Pammy.flac',
    size: 1234,
    deleted_at: null,
    source,
    original_path: '/app/storage/album_bundle_staging/b_stalled/01-02 New Pammy.flac',
  });
  const handlers = { onRestore: vi.fn(), onPurge: vi.fn() };

  it('names a rescued album bundle so it is not an unexplained file (#1210)', () => {
    const { container } = render(
      <AdlDeletedRow entry={entry('album_bundle_orphan')} handlers={handlers} />,
    );
    expect(container.textContent).toContain('Stalled album download');
  });

  it('still names the two older sources', () => {
    const { container } = render(<AdlDeletedRow entry={entry('repair')} handlers={handlers} />);
    expect(container.textContent).toContain('Repair tool');
    cleanup();
    const dupe = render(<AdlDeletedRow entry={entry('duplicate-cleaner')} handlers={handlers} />);
    expect(dupe.container.textContent).toContain('Duplicate cleaner');
  });

  it('says nothing rather than guessing for an unknown source', () => {
    const { container } = render(<AdlDeletedRow entry={entry(null)} handlers={handlers} />);
    expect(container.querySelector('.adl-row-batch')).toBeNull();
  });
});
