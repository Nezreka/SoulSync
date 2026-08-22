/**
 * Activity — the two histories under one roof.
 *
 * What matters is the SPLIT: syncs you ran versus runs the schedule ran. Those
 * were two buttons answering the same question, and you had to know which one
 * had touched your playlist before you knew where to look.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ACTIVITY_TAB_LABELS, ActivityModal, type ActivityModalProps } from './activity-modal';

function renderModal(over: Partial<ActivityModalProps> = {}) {
  const props: ActivityModalProps = {
    open: true,
    tab: 'syncs',
    onTab: vi.fn(),
    onClose: vi.fn(),
    now: Date.parse('2026-08-21T12:00:00Z'),
    syncs: {
      entries: [
        {
          id: 7,
          playlist_name: 'Road Trip',
          source: 'spotify',
          completed_at: 'x',
          tracks_found: 3,
        },
      ],
      stats: { spotify: 1 },
      total: 1,
      page: 1,
      pageSize: 20,
      source: null,
      loading: false,
      error: '',
      resyncs: {},
      onSelectSource: vi.fn(),
      onPage: vi.fn(),
      onResync: vi.fn(),
      onCancel: vi.fn(),
      onDelete: vi.fn(),
    },
    runs: {
      history: [],
      total: 0,
      filter: 'all',
      onFilterChange: vi.fn(),
      onLoadMore: vi.fn(),
      onRunAgain: vi.fn(),
    },
    ...over,
  };
  return { props, ...render(<ActivityModal {...props} />) };
}

describe('the two tabs', () => {
  it('names them by WHO ran the sync, which is the real split', () => {
    expect(ACTIVITY_TAB_LABELS).toEqual({
      syncs: 'Syncs you ran',
      runs: 'Scheduled runs',
    });
  });

  it('shows the sync history on the first tab', () => {
    renderModal();
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
  });

  it('shows the pipeline runs on the second, not the sync history', () => {
    renderModal({ tab: 'runs' });
    expect(screen.queryByText('Road Trip')).toBeNull();
    expect(screen.getByText(/No playlist pipeline runs yet/)).toBeInTheDocument();
  });

  it('switching asks the caller rather than holding its own tab state', () => {
    const { props } = renderModal();
    fireEvent.click(screen.getByText('Scheduled runs'));
    expect(props.onTab).toHaveBeenCalledWith('runs');
  });

  it('marks the open tab for assistive tech', () => {
    const { container } = renderModal({ tab: 'runs' });
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toContain(
      'Scheduled runs',
    );
  });
});

describe('the failed-run badge', () => {
  it('appears on the runs tab when something failed', () => {
    const { container } = renderModal({ failedRuns: 3 });
    expect(container.querySelector('.sync-activity-tab-badge')?.textContent).toBe('3');
  });

  it('is absent at zero — a badge reading 0 is a badge saying nothing', () => {
    const { container } = renderModal({ failedRuns: 0 });
    expect(container.querySelector('.sync-activity-tab-badge')).toBeNull();
  });

  it('never lands on the syncs tab', () => {
    const { container } = renderModal({ failedRuns: 2 });
    const syncsTab = [...container.querySelectorAll('[role="tab"]')].find((t) =>
      t.textContent?.startsWith('Syncs you ran'),
    );
    expect(syncsTab?.querySelector('.sync-activity-tab-badge')).toBeNull();
  });
});

describe('dismissal', () => {
  it('closes from the ×, the overlay and Escape', () => {
    const { props, container } = renderModal();
    fireEvent.click(screen.getByLabelText('Close'));
    expect(props.onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(container.querySelector('.sync-activity-overlay') as HTMLElement);
    expect(props.onClose).toHaveBeenCalledTimes(2);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(props.onClose).toHaveBeenCalledTimes(3);
  });

  it('a click INSIDE the modal does not close it', () => {
    const { props, container } = renderModal();
    fireEvent.click(container.querySelector('.sync-activity-modal') as HTMLElement);
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it('renders nothing at all until it has been opened once', () => {
    const { container } = renderModal({ open: false });
    expect(container.querySelector('.sync-activity-overlay')).toBeNull();
  });

  it('stops listening for Escape once closed', () => {
    const onClose = vi.fn();
    const { rerender } = renderModal({ onClose });
    rerender(
      <ActivityModal
        {...renderModal({ open: false, onClose }).props}
        open={false}
        onClose={onClose}
      />,
    );
    onClose.mockClear();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
