import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';

import { FindingsInbox } from './findings-inbox';

// review 5 R5-L-3: under a severity filter a group row counts and colours the
// rows of that severity, not the whole status or the worst pending row
describe('findings inbox under a severity filter', () => {
  const metadataGap: FindingGroup = {
    finding_type: 'metadata_gap',
    pending: 301,
    resolved: 0,
    dismissed: 0,
    total: 301,
    severity_max: 'error',
    severities: { pending: { warning: 300, error: 1 } },
  };

  const renderInbox = (severity?: string) =>
    render(
      <FindingsInbox
        groups={[metadataGap]}
        typeInfo={() => undefined}
        status="pending"
        severity={severity}
        openType=""
        onToggleOpen={vi.fn()}
        onFixGroup={vi.fn()}
        onDismissGroup={vi.fn()}
        onReopenGroup={vi.fn()}
        busy={false}
      />,
    );

  it('counts the warnings, not every pending row', () => {
    const { container } = renderInbox('warning');
    expect(container.querySelector('.repair-inbox-count')?.textContent).toBe('300');
    expect(container.querySelector('.repair-inbox-sev')?.classList.contains('warning')).toBe(true);
  });

  it('with no severity filter it still shows the whole status', () => {
    const { container } = renderInbox();
    expect(container.querySelector('.repair-inbox-count')?.textContent).toBe('301');
    expect(container.querySelector('.repair-inbox-sev')?.classList.contains('error')).toBe(true);
  });
});

// the inbox sorts types by what fixing them would do, and opening one shows
// that type alone with a way back
describe('findings inbox sections', () => {
  const group = (finding_type: string, over: Partial<FindingGroup> = {}): FindingGroup => ({
    finding_type,
    pending: 5,
    resolved: 0,
    dismissed: 0,
    total: 5,
    severity_max: 'info',
    ...over,
  });
  const catalog: Record<string, FindingTypeInfo> = {
    missing_lyrics: {
      type: 'missing_lyrics',
      label: 'Missing Lyrics',
      verb: 'Apply',
      fixable: true,
      destructive: false,
    },
    orphan_file: {
      type: 'orphan_file',
      label: 'Orphan Files',
      verb: 'Move',
      fixable: true,
      destructive: true,
    },
    quality_upgrade: {
      type: 'quality_upgrade',
      label: 'Upgrades',
      verb: 'Grab',
      fixable: true,
      destructive: false,
      acquires: true,
    },
    album_needs_enrichment: {
      type: 'album_needs_enrichment',
      label: 'Needs Enrichment',
      verb: null,
      fixable: false,
      destructive: false,
    },
  };

  const renderInbox = (groups: FindingGroup[], openType = '', onToggleOpen = vi.fn()) =>
    render(
      <FindingsInbox
        groups={groups}
        typeInfo={(type) => catalog[type]}
        status="pending"
        openType={openType}
        onToggleOpen={onToggleOpen}
        onFixGroup={vi.fn()}
        onDismissGroup={vi.fn()}
        onReopenGroup={vi.fn()}
        busy={false}
      >
        <div data-testid="the-list" />
      </FindingsInbox>,
    );

  const sectionsOf = (container: HTMLElement) =>
    [...container.querySelectorAll('.repair-triage')].map((section) => ({
      title: section.querySelector('.tools-section-title')?.textContent,
      count: section.querySelector('.repair-triage-count')?.textContent,
      types: [...section.querySelectorAll('.repair-inbox-group')].map((node) =>
        node.getAttribute('data-finding-type'),
      ),
    }));

  it('puts each type where its fix belongs, with the section total', () => {
    const { container } = renderInbox([
      group('missing_lyrics', { pending: 40 }),
      group('orphan_file', { pending: 7 }),
      group('quality_upgrade', { pending: 3 }),
      group('album_needs_enrichment', { pending: 2 }),
    ]);
    expect(sectionsOf(container)).toEqual([
      { title: 'Safe to fix', count: '40', types: ['missing_lyrics'] },
      // a download starts, or a file is moved: both ask you first, and the
      // one that touches files sits last
      { title: 'Needs your call', count: '10', types: ['quality_upgrade', 'orphan_file'] },
      { title: 'Review only', count: '2', types: ['album_needs_enrichment'] },
    ]);
  });

  it('leaves out a section with nothing in it', () => {
    const { container } = renderInbox([group('missing_lyrics')]);
    expect(sectionsOf(container).map((section) => section.title)).toEqual(['Safe to fix']);
  });

  it('leads with the section that holds the worst finding', () => {
    const { container } = renderInbox([
      group('missing_lyrics'),
      group('orphan_file', { severity_max: 'error' }),
    ]);
    expect(sectionsOf(container).map((section) => section.title)).toEqual([
      'Needs your call',
      'Safe to fix',
    ]);
  });

  it('shows the covers a type came with, and none when it has none', () => {
    const { container } = renderInbox([
      group('missing_lyrics', { art: ['/a.jpg', '/b.jpg'] }),
      group('orphan_file'),
    ]);
    const stack = (type: string) =>
      [...container.querySelectorAll(`#repair-group-${type} .repair-art-mosaic img`)].map((img) =>
        img.getAttribute('src'),
      );
    expect(stack('missing_lyrics')).toEqual(['/a.jpg', '/b.jpg']);
    expect(stack('orphan_file')).toEqual([]);
    // the tile is still there, with a glyph, so the rows share one edge
    expect(
      container.querySelector('#repair-group-orphan_file .repair-art-mosaic.empty svg'),
    ).not.toBeNull();
  });

  it('a cover that fails to load is dropped, not left as a broken picture', () => {
    const { container } = renderInbox([group('missing_lyrics', { art: ['/a.jpg', '/b.jpg'] })]);
    fireEvent.error(container.querySelector('.repair-art-mosaic img[src="/a.jpg"]') as Element);
    expect(
      [...container.querySelectorAll('.repair-art-mosaic img')].map((img) =>
        img.getAttribute('src'),
      ),
    ).toEqual(['/b.jpg']);
  });

  it('an opened type is shown alone, above its list, with a way back', () => {
    const onToggleOpen = vi.fn();
    const { container, getByRole, getByTestId } = renderInbox(
      [group('missing_lyrics'), group('orphan_file')],
      'orphan_file',
      onToggleOpen,
    );
    expect(
      [...container.querySelectorAll('.repair-inbox-group')].map((node) =>
        node.getAttribute('data-finding-type'),
      ),
    ).toEqual(['orphan_file']);
    expect(container.querySelector('.repair-triage')).toBeNull();
    expect(getByTestId('the-list').closest('.repair-inbox-body')).not.toBeNull();

    fireEvent.click(getByRole('button', { name: 'All findings' }));
    expect(onToggleOpen).toHaveBeenCalledWith('orphan_file');
  });

  it("an opened type's header is not a button: a stray click must not close the list", () => {
    const onToggleOpen = vi.fn();
    const { container } = renderInbox([group('orphan_file')], 'orphan_file', onToggleOpen);
    const head = container.querySelector('.repair-inbox-head') as HTMLElement;
    expect(head.getAttribute('role')).toBeNull();
    fireEvent.click(head);
    expect(onToggleOpen).not.toHaveBeenCalled();
    // its bulk actions are still there
    expect(container.querySelector('.repair-inbox-btn')?.textContent).toBe('Move all (5)');
  });

  it('a closed row opens on click and on Enter', () => {
    const onToggleOpen = vi.fn();
    const { container } = renderInbox([group('orphan_file')], '', onToggleOpen);
    const head = container.querySelector('.repair-inbox-head') as HTMLElement;
    expect(head.getAttribute('role')).toBe('button');
    fireEvent.click(head);
    fireEvent.keyDown(head, { key: 'Enter' });
    expect(onToggleOpen).toHaveBeenCalledTimes(2);
  });
});
