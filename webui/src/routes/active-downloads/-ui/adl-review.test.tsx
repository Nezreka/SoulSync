import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdlDeletedEntry, AdlDownload, AdlQuarantineEntry } from '../-adl.types';
import type { ReviewActionHandlers } from './adl-review';

import {
  AdlDeletedRow,
  AdlQuarantineList,
  AdlQuarantineRow,
  AdlReviewBanner,
  AdlReviewExplainer,
  AdlReviewSelectionBar,
  AdlUnverifiedRow,
  quarantineSummary,
} from './adl-review';

afterEach(cleanup);

const handlers = (): ReviewActionHandlers => ({
  onPlay: vi.fn(),
  onCompare: vi.fn(),
  onAudit: vi.fn(),
  onApprove: vi.fn(),
  onDelete: vi.fn(),
});

const stamp = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

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
    created_at: stamp(5 * 60_000),
    ...over,
  }) as AdlDownload;

const entry = (over: Partial<AdlQuarantineEntry> = {}): AdlQuarantineEntry =>
  ({
    id: 'q1',
    filename: 'bad.quarantined',
    original_filename: '03 Xtal.flac',
    reason: 'duration mismatch',
    expected_track: 'Xtal',
    expected_artist: 'Aphex Twin',
    group_key: 'g1',
    timestamp: stamp(60_000),
    size_bytes: 1000,
    has_full_context: true,
    trigger: 'integrity',
    source_username: 'somepeer',
    source_filename: 'peer/bad.flac',
    thumb_url: '',
    quality: 'FLAC',
    ...over,
  }) as AdlQuarantineEntry;

const rowEl = (c: HTMLElement) => c.querySelector('.dlc-row') as HTMLElement;
const openMenu = (c: HTMLElement) => fireEvent.click(c.querySelector('.dlc-more') as HTMLElement);

describe('AdlUnverifiedRow', () => {
  it('tags the reason, the quality and when', () => {
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    const tags = [...container.querySelectorAll('.dlc-tag')].map((t) => t.textContent);
    expect(tags).toEqual(['Not confirmed', 'FLAC']);
    expect(container.querySelector('.dlc-tag-warn')).not.toBeNull();
    expect(container.querySelector('.dlc-when')?.textContent).toBe('5m ago');
  });

  it('omits the time when the row has no timestamp', () => {
    // live task rows carry no created_at; an empty span would be noise
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl({ created_at: undefined })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.dlc-when')).toBeNull();
  });

  it('marks a force-imported row differently from an unconfirmed one', () => {
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl({ verification_status: 'force_imported' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.dlc-tag-info')?.textContent).toBe('Force-imported');
  });

  it('opens the sheet only when open, with why it is here', () => {
    const closed = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(closed.container.querySelector('.dlc-sheet')).toBeNull();
    cleanup();
    const open = render(
      <AdlUnverifiedRow dl={dl()} open onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(open.container.querySelector('.dlc-step-warn')?.textContent).toContain(
      "AcoustID couldn't confirm it",
    );
  });

  it('shows the file only for a persistent-history row', () => {
    const withFile = render(
      <AdlUnverifiedRow
        dl={dl({ file_path: '/music/x.flac' })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(withFile.container.querySelector('.dlc-facts')?.textContent).toContain('/music/x.flac');
    cleanup();
    const live = render(
      <AdlUnverifiedRow
        dl={dl({ file_path: undefined })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(live.container.querySelector('.dlc-facts')?.textContent ?? '').not.toContain('File');
  });

  it('puts what we asked for beside what we got, the difference lit', () => {
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl({
          title: 'Chun-Li Theme',
          expected_title: 'Chun-Li: Woman of Vengeance',
          expected_artist: 'Aphex Twin',
        })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    const diffs = [...container.querySelectorAll('.dlc-compare-diff')].map((d) => d.textContent);
    expect(diffs).toEqual(['Chun-Li Theme']);
  });

  it('toggles on a row click, never on an action click', () => {
    const onToggle = vi.fn();
    const acts = handlers();
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={onToggle} handlers={acts} />,
    );
    fireEvent.click(rowEl(container));
    expect(onToggle).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(acts.onApprove).toHaveBeenCalled();
    fireEvent.click(container.querySelector('.dlc-play') as HTMLElement);
    expect(acts.onPlay).toHaveBeenCalled();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('keeps delete in the menu, marked dangerous', () => {
    const acts = handlers();
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={acts} />,
    );
    openMenu(container);
    const del = screen.getByRole('menuitem', { name: 'Delete file' });
    expect(del.className).toContain('danger');
    fireEvent.click(del);
    expect(acts.onDelete).toHaveBeenCalled();
  });

  it('renders no actions without a history id to act on', () => {
    const { container } = render(
      <AdlUnverifiedRow dl={dl()} open={false} onToggle={vi.fn()} handlers={null} />,
    );
    expect(container.querySelector('.dlc-acts button')).toBeNull();
    // the row still renders: it is informational, not missing
    expect(container.querySelector('.dlc-title')?.textContent).toBe('Xtal');
  });

  it('locks compare while the search runs', () => {
    const acts = handlers();
    acts.onCompare = (setBusy) => setBusy(true);
    render(<AdlUnverifiedRow dl={dl()} open onToggle={vi.fn()} handlers={acts} />);
    const compare = screen.getByRole('button', { name: 'Compare with a stream' });
    fireEvent.click(compare);
    const busy = screen.getByRole('button', { name: 'Finding a stream…' }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
  });

  it('selects from the art corner without opening the row', () => {
    const onSelect = vi.fn();
    const onToggle = vi.fn();
    const { container } = render(
      <AdlUnverifiedRow
        dl={dl()}
        open={false}
        onToggle={onToggle}
        handlers={handlers()}
        selected
        onSelect={onSelect}
      />,
    );
    const box = container.querySelector('.dlc-check') as HTMLElement;
    expect(box.getAttribute('aria-checked')).toBe('true');
    expect(rowEl(container).className).toContain('dlc-row-sel');
    fireEvent.click(box);
    expect(onSelect).toHaveBeenCalled();
    expect(onToggle).not.toHaveBeenCalled();
  });
});

describe('AdlQuarantineRow', () => {
  it('names the trigger in words, falling back to Quarantined', () => {
    const { container } = render(
      <AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(container.querySelector('.dlc-tag-bad')?.textContent).toBe('Failed integrity check');
    cleanup();
    const unknown = render(
      <AdlQuarantineRow
        entry={entry({ trigger: 'brand_new' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(unknown.container.querySelector('.dlc-tag-bad')?.textContent).toBe('Quarantined');
  });

  it('names the uploader for a Soulseek file and the service otherwise', () => {
    const peer = render(
      <AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={handlers()} />,
    );
    expect(peer.container.querySelector('.dlc-sub')?.textContent).toBe(
      'Aphex Twin · Soulseek · from somepeer',
    );
    cleanup();
    const service = render(
      <AdlQuarantineRow
        entry={entry({ source_username: 'tidal' })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(service.container.querySelector('.dlc-sub')?.textContent).toBe('Aphex Twin · Tidal');
  });

  it('leads with Approve with full context, Recover without', () => {
    const acts = handlers();
    render(<AdlQuarantineRow entry={entry()} open={false} onToggle={vi.fn()} handlers={acts} />);
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve.getAttribute('title')).toContain('exact file');
    fireEvent.click(approve);
    expect(acts.onApprove).toHaveBeenCalled();
    cleanup();
    render(
      <AdlQuarantineRow
        entry={entry({ has_full_context: false })}
        open={false}
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Recover' }).getAttribute('title')).toContain(
      'Recover to Staging',
    );
  });

  it('leads with Delete when the track is already in the library, and says why', () => {
    const acts = handlers();
    const owned = entry({
      library_status: 'present',
      library_match: {
        album: 'SAW',
        filename: 'Xtal.flac',
        quality: 'FLAC 16-bit',
        verification_status: 'human_verified',
      },
    });
    const { container } = render(
      <AdlQuarantineRow entry={owned} open onToggle={vi.fn()} handlers={acts} />,
    );
    expect(container.querySelector('.dlc-owned')?.getAttribute('title')).toBe(
      'SAW · FLAC 16-bit · human verified',
    );
    expect(container.querySelector('.dlc-callout')?.textContent).toContain(
      'You already have this track.',
    );
    fireEvent.click(container.querySelector('.dlc-acts .dlc-btn-primary') as HTMLElement);
    expect(acts.onDelete).toHaveBeenCalled();
    // approving is still there, just not first
    expect(screen.getAllByRole('button', { name: 'Approve anyway' }).length).toBeGreaterThan(0);
  });

  it('marks an extra track from an album import, and explains the library check', () => {
    const { container, rerender } = render(
      <AdlQuarantineRow
        entry={entry({ album_extra: { album: 'SAW', download_source: 'usenet' } })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    const tag = [...container.querySelectorAll('.dlc-tag')].find(
      (t) => t.textContent === 'Album extra',
    );
    expect(tag?.getAttribute('title')).toContain('Usenet album import');
    rerender(
      <AdlQuarantineRow
        entry={entry({ library_status: 'unknown' })}
        open
        onToggle={vi.fn()}
        handlers={handlers()}
      />,
    );
    expect(container.querySelector('.dlc-facts')?.textContent).toContain(
      'Library check unavailable',
    );
  });

  it('compares the expected title with the file name, minus number and extension', () => {
    const { container } = render(
      <AdlQuarantineRow entry={entry()} open onToggle={vi.fn()} handlers={handlers()} />,
    );
    // "03 Xtal.flac" is the same track as "Xtal": nothing lit
    expect(container.querySelector('.dlc-compare-diff')).toBeNull();
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
    expect(container.querySelectorAll('[data-quarantine-id]')).toHaveLength(1);
    expect(container.querySelector('.dlc-qhead')).toBeNull();
  });

  it('folds several attempts under one header that says how many and why', () => {
    // six rejected candidates for one track should cost one line, not six
    const members = [entry(), entry({ id: 'q2', trigger: 'acoustid' }), entry({ id: 'q3' })];
    const { container } = render(
      <AdlQuarantineList {...listProps} groups={[{ key: 'g1', members }]} />,
    );
    const head = container.querySelector('.dlc-qhead') as HTMLElement;
    expect(head.getAttribute('aria-expanded')).toBe('false');
    expect(head.querySelector('.dlc-attempts')?.textContent).toBe('3 attempts');
    expect(head.querySelector('.dlc-sub')?.textContent).toBe(
      'Aphex Twin · Failed integrity check, AcoustID mismatch',
    );
    expect(container.querySelectorAll('[data-quarantine-id]')).toHaveLength(0);
  });

  it('opens the attempts when the group is expanded', () => {
    const onToggleGroup = vi.fn();
    const members = [entry(), entry({ id: 'q2' })];
    const { container } = render(
      <AdlQuarantineList
        {...listProps}
        onToggleGroup={onToggleGroup}
        openGroups={new Set(['g1'])}
        groups={[{ key: 'g1', members }]}
      />,
    );
    expect(container.querySelectorAll('[data-quarantine-id]')).toHaveLength(2);
    fireEvent.click(container.querySelector('.dlc-qhead') as HTMLElement);
    expect(onToggleGroup).toHaveBeenCalledWith('g1');
  });

  it('labels group counts as a floor when the list is partial, and deletes by group', () => {
    const onDeleteGroup = vi.fn();
    const members = [entry(), entry({ id: 'q2' }), entry({ id: 'q3' })];
    render(
      <AdlQuarantineList
        {...listProps}
        partial
        onDeleteGroup={onDeleteGroup}
        groups={[{ key: 'g1', members }]}
      />,
    );
    expect(screen.getByText('3+ attempts').getAttribute('title')).toBe(
      'At least 3 downloads tried to be this track (the list is paged)',
    );
    const del = screen.getByRole('button', { name: 'Delete all attempts' });
    expect(del.getAttribute('title')).toContain('including ones not loaded yet');
    fireEvent.click(del);
    // the server resolves the full group, so the count is unknown here
    expect(onDeleteGroup).toHaveBeenCalledWith(members[0], null);
  });

  it('gives a total when the list is whole, without toggling the group', () => {
    const onDeleteGroup = vi.fn();
    const onToggleGroup = vi.fn();
    const members = [entry(), entry({ id: 'q2' }), entry({ id: 'q3' })];
    render(
      <AdlQuarantineList
        {...listProps}
        onToggleGroup={onToggleGroup}
        onDeleteGroup={onDeleteGroup}
        groups={[{ key: 'g1', members }]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Delete all 3' }));
    expect(onDeleteGroup).toHaveBeenCalledWith(members[0], 3);
    expect(onToggleGroup).not.toHaveBeenCalled();
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
    onRestoreAllDeleted: vi.fn(),
    onEmptyDeleted: vi.fn(),
  };
  const segs = (c: HTMLElement) =>
    [...c.querySelectorAll('.dlc-seg-btn')].map((p) => p.textContent);
  const menuItems = (c: HTMLElement) => {
    const more = c.querySelector('.dlc-more');
    if (!more) return [];
    fireEvent.click(more);
    return screen.getAllByRole('menuitem').map((m) => m.textContent);
  };

  it('shows the three sub-views with counts', () => {
    const { container } = render(<AdlReviewBanner {...props} />);
    expect(segs(container)).toEqual(['Unverified3', 'Quarantine2', 'Deleted4']);
  });

  it('hides Unverified when no such queue can exist', () => {
    const { container } = render(<AdlReviewBanner {...props} acoustidEnabled={false} />);
    // the deleted bin exists regardless of acoustid; only unverified hides
    expect(segs(container)).toEqual(['Quarantine2', 'Deleted4']);
  });

  it('holds back a count until it is known', () => {
    // showing 0 before the fetch lands would read as "none quarantined"
    const { container } = render(
      <AdlReviewBanner {...props} quarantineLoaded={false} deletedCount={null} />,
    );
    expect(segs(container)).toEqual(['Unverified3', 'Quarantine', 'Deleted']);
  });

  it('says tracks AND files over the quarantine list, so 7 over two rows adds up', () => {
    const { container } = render(
      <AdlReviewBanner
        {...props}
        subView="quarantine"
        quarantineCount={7}
        quarantineTrackCount={2}
      />,
    );
    expect(container.querySelector('.dlc-subbar-note')?.textContent).toBe('2 tracks · 7 files');
  });

  it('keeps the summary to files when nothing is grouped', () => {
    expect(quarantineSummary(3, 3)).toBe('3 files');
    expect(quarantineSummary(1, 1)).toBe('1 file');
    expect(quarantineSummary(1, 4)).toBe('1 track · 4 files');
  });

  it('offers Clean orphaned only for unverified files', () => {
    const unv = render(<AdlReviewBanner {...props} />);
    expect(menuItems(unv.container)).toEqual([
      'Approve all (3)',
      'Clean orphaned entries',
      'Delete all',
    ]);
    cleanup();
    // a quarantined file IS the file. it can't be orphaned
    const quar = render(<AdlReviewBanner {...props} subView="quarantine" />);
    expect(menuItems(quar.container)).toEqual(['Approve all (2)', 'Delete all']);
  });

  it('has no bulk menu over an empty view, a promise the click cannot keep', () => {
    const unv = render(<AdlReviewBanner {...props} unverifiedCount={0} />);
    expect(unv.container.querySelector('.dlc-more')).toBeNull();
    cleanup();
    const del = render(<AdlReviewBanner {...props} subView="deleted" deletedCount={0} />);
    expect(del.container.querySelector('.dlc-more')).toBeNull();
  });

  it('routes each bulk action to its own handler', () => {
    const onApproveAll = vi.fn();
    const onDeleteAll = vi.fn();
    const { container } = render(
      <AdlReviewBanner {...props} onApproveAll={onApproveAll} onDeleteAll={onDeleteAll} />,
    );
    fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Approve all (3)' }));
    fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete all' }));
    expect(onApproveAll).toHaveBeenCalled();
    expect(onDeleteAll).toHaveBeenCalled();
  });
});

describe('selection and explainers', () => {
  it('floats the selected rows actions, and only with a selection', () => {
    const onApprove = vi.fn();
    const { container, rerender } = render(
      <AdlReviewSelectionBar
        count={0}
        onApprove={onApprove}
        onDelete={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(container.querySelector('.dlc-selbar')).toBeNull();
    rerender(
      <AdlReviewSelectionBar
        count={2}
        onApprove={onApprove}
        onDelete={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(container.querySelector('.dlc-selbar-n')?.textContent).toBe('2 selected');
    fireEvent.click(screen.getByRole('button', { name: 'Approve 2' }));
    expect(onApprove).toHaveBeenCalled();
  });

  it('says what each view is, plainly, every time', () => {
    const { container, rerender } = render(<AdlReviewExplainer subView="unverified" />);
    expect(container.textContent).toContain("Imported, but AcoustID couldn't confirm them.");
    rerender(<AdlReviewExplainer subView="quarantine" />);
    expect(container.textContent).toContain('never imported');
    rerender(<AdlReviewExplainer subView="deleted" />);
    expect(container.textContent).toContain('Restore puts a file back');
  });
});

describe('AdlDeletedRow', () => {
  const del = (source: string | null): AdlDeletedEntry => ({
    id: 'd1',
    name: '01-02 New Pammy.flac',
    rel: 'album_bundle_orphans/b_stalled/01-02 New Pammy.flac',
    size: 1234,
    deleted_at: null,
    source,
    original_path: '/app/storage/album_bundle_staging/b_stalled/01-02 New Pammy.flac',
  });
  const rowHandlers = () => ({ onRestore: vi.fn(), onPurge: vi.fn() });

  it('names a rescued album bundle so it is not an unexplained file (#1210)', () => {
    const { container } = render(
      <AdlDeletedRow entry={del('album_bundle_orphan')} handlers={rowHandlers()} />,
    );
    expect(container.querySelector('.dlc-tag')?.textContent).toBe('Stalled album download');
  });

  it('still names the older sources, and says nothing for an unknown one', () => {
    const { container, rerender } = render(
      <AdlDeletedRow entry={del('repair')} handlers={rowHandlers()} />,
    );
    expect(container.querySelector('.dlc-tag')?.textContent).toBe('Repair tool');
    rerender(<AdlDeletedRow entry={del('duplicate-cleaner')} handlers={rowHandlers()} />);
    expect(container.querySelector('.dlc-tag')?.textContent).toBe('Duplicate cleaner');
    rerender(<AdlDeletedRow entry={del(null)} handlers={rowHandlers()} />);
    expect(container.querySelector('.dlc-tag')).toBeNull();
  });

  it('restores with one click and keeps delete-forever in the menu', () => {
    const h = rowHandlers();
    const { container } = render(<AdlDeletedRow entry={del(null)} handlers={h} />);
    expect(container.querySelector('.dlc-file')?.textContent).toBe('FLAC');
    expect(container.querySelector('.dlc-when')?.textContent).toBe('age unknown');
    openMenu(container);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete forever' }));
    expect(h.onPurge).toHaveBeenCalled();
    cleanup();
    const h2 = rowHandlers();
    render(<AdlDeletedRow entry={del(null)} handlers={h2} />);
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    expect(h2.onRestore).toHaveBeenCalled();
  });
});
